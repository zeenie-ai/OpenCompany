/**
 * Normal mode's sidebar (design handoff "Sidebar"): the logo, "New
 * employee", the team, and the owner's profile. Collapses to nothing
 * (width + opacity); the header then shows the logo and an open button.
 */

import { useLayoutEffect, useRef } from 'react';
import { PanelLeft, Plus, Settings } from 'lucide-react';
import { OcLogo } from '@/components/brand/Logo';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useEmployeesQuery } from '../data/employees';
import { presentEmployee } from '../data/presentation';
import { useOwnerSettings } from '../data/profile';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';
import { Avatar, MicroLabel } from '../ui/primitives';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';

function EmployeeRow({ employee, selected }: { employee: EmployeeSummary; selected: boolean }) {
  const showEmployee = useHomeStore((s) => s.showEmployee);
  const glow = useHomeStore((s) => (s.glow?.workflowId === employee.workflow_id ? s.glow.nonce : 0));
  const pending = useWorkflowControlPending(employee.workflow_id);
  const ref = useRef<HTMLButtonElement>(null);
  const view = presentEmployee(employee, pending);

  // A new hire drops into the list with a green glow (design handoff "Hire").
  useLayoutEffect(() => {
    if (!glow) return;
    animate(
      ref.current,
      [
        {
          opacity: 0,
          transform: 'translateY(-24px) scale(.9)',
          boxShadow: 'var(--glow-hire)',
        },
        {
          opacity: 1,
          transform: 'none',
          boxShadow: 'var(--glow-hire-settle)',
          offset: 0.6,
        },
        { opacity: 1, transform: 'none', boxShadow: '0 0 0 0 transparent' },
      ],
      { duration: 'glow', easing: 'spring', fill: 'backwards' },
    );
  }, [glow]);

  return (
    <button
      ref={ref}
      type="button"
      data-employee-row={employee.workflow_id}
      aria-current={selected ? 'page' : undefined}
      onClick={() => showEmployee(employee.workflow_id)}
      className={cn(
        'flex min-h-12.5 w-full items-center gap-2.5 rounded-row px-2.5 py-1.5 text-left text-fg-default transition-colors hover:bg-bg-hover',
        selected && 'bg-bg-hover',
      )}
    >
      <Avatar name={employee.name} colorRole={employee.color_role} status={view.pill.tone} pulse={view.pulse} />
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="truncate text-row font-medium">{employee.name}</span>
        <span className="truncate text-xs text-fg-muted">{employee.role}</span>
      </span>
      {employee.pending_approvals > 0 && (
        <span
          className="grid h-5 min-w-5 shrink-0 place-items-center rounded-pill border border-status-waiting-border bg-status-waiting-fill px-1.5 font-mono text-2xs font-semibold text-status-waiting-ink"
          aria-label={`${employee.pending_approvals} waiting for you`}
        >
          {employee.pending_approvals}
        </span>
      )}
    </button>
  );
}

function TeamList() {
  const { data: employees, isLoading, isError, refetch } = useEmployeesQuery();
  const view = useHomeStore((s) => s.view);
  const selectedId = view.kind === 'employee' ? view.workflowId : null;

  return (
    <>
      <div className="flex items-center px-2.5 pt-4.5 pb-1.5">
        <MicroLabel>AI employees</MicroLabel>
        <span className="ml-auto font-mono text-2xs font-medium tracking-label text-fg-faint">{employees?.length ?? ''}</span>
      </div>
      <nav aria-label="AI employees" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-x-hidden overflow-y-auto">
        {isLoading &&
          [0, 1, 2].map((i) => (
            <div key={i} className="flex min-h-12.5 items-center gap-2.5 px-2.5">
              <Skeleton className="size-8 rounded-full" />
              <div className="flex flex-1 flex-col gap-1.5">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-2.5 w-32" />
              </div>
            </div>
          ))}
        {isError && (
          <button type="button" onClick={() => void refetch()} className="px-2.5 py-2 text-left text-xs text-fg-muted hover:text-fg-default">
            Couldn’t load your team. Try again
          </button>
        )}
        {employees?.length === 0 && (
          <p className="px-2.5 py-2 text-xs text-fg-faint">Nobody on the team yet. Describe a job to hire your first employee.</p>
        )}
        {employees?.map((employee) => (
          <EmployeeRow key={employee.workflow_id} employee={employee} selected={employee.workflow_id === selectedId} />
        ))}
      </nav>
    </>
  );
}

function ProfileRow() {
  const openSettings = useHomeStore((s) => s.openSettings);
  const { data: settings } = useOwnerSettings();
  const name = String(settings?.profile_full_name ?? '').trim();
  return (
    <div className="border-t border-border-default pt-2">
      <button
        type="button"
        onClick={() => openSettings('profile')}
        className="flex w-full items-center gap-2.5 rounded-row p-2 text-left text-fg-default transition-colors hover:bg-bg-hover"
      >
        <Avatar name={name || 'You'} colorRole="agent" size="sm" />
        <span className="truncate text-sm font-medium">{name || 'Your profile'}</span>
        <Settings aria-hidden className="ml-auto size-3.75 shrink-0 text-fg-faint" />
        <span className="sr-only">Settings</span>
      </button>
    </div>
  );
}

export function HomeSidebar() {
  const open = useHomeStore((s) => s.sidebarOpen);
  const toggleSidebar = useHomeStore((s) => s.toggleSidebar);
  const showHire = useHomeStore((s) => s.showHire);
  const logoPulse = useHomeStore((s) => s.logoPulse);

  return (
    <aside
      aria-label="Your team"
      aria-hidden={!open}
      inert={!open}
      className={cn(
        'flex shrink-0 flex-col gap-1 overflow-hidden border-border-default bg-bg-panel whitespace-nowrap transition-[width,opacity,padding] motion-reduce:transition-none',
        open
          ? 'w-(--w-home-sidebar) border-r p-3 opacity-100 duration-(--dur-sidebar-in) ease-spring'
          : 'w-0 border-r-0 p-0 opacity-0 duration-(--dur-sidebar-out) ease-(--ease-default)',
      )}
    >
      <div className="flex items-center px-1 pt-1 pb-3.5">
        <OcLogo size="sidebar" intro pulseNonce={logoPulse} />
        <Button
          variant="quiet"
          size="icon-sm"
          onClick={toggleSidebar}
          aria-label="Close sidebar"
          title="Close sidebar"
          className="ml-auto size-7.5 rounded-lg"
        >
          <PanelLeft className="size-4.25" strokeWidth={1.75} />
        </Button>
      </div>
      <ActionButton
        intent="run"
        onClick={() => showHire({ focus: true })}
        className="h-9.5 w-full justify-start gap-2.5 rounded-row px-3"
      >
        <Plus aria-hidden className="size-4" />
        New employee
      </ActionButton>
      <TeamList />
      <ProfileRow />
    </aside>
  );
}

export default HomeSidebar;
