/**
 * Normal mode's header (design handoff "Main header"; chat "Header"): with
 * the sidebar collapsed, an open button and the logo. Then the view: while
 * hiring, its title; on an employee's page, who they are (avatar, name,
 * role and apps, their status) and New conversation. The owner renames them
 * with the pencil beside their name, and gives them a photo (or takes it
 * away) from their avatar's menu; an employee built in the editor keeps its
 * initial. At the end the Workspace pill, the Normal/Dev switch (on an
 * employee's page, Dev opens their workflow) and the theme button. The
 * bottom border appears only once the content has scrolled, so the hero
 * reads as one surface.
 */

import { useRef, useState } from 'react';
import { ImagePlus, PanelLeft, Pencil, Trash2 } from 'lucide-react';
import { OcLogo } from '@/components/brand/Logo';
import { ModeToggle } from '@/components/shell/ModeToggle';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';
import { PHOTO_TYPES, useRenameEmployee, useSetEmployeePhoto } from '../data/identity';
import { useAnswering } from '../data/liveTask';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { HIRE_LIMITS } from '../genui';
import { useHomeStore } from '../state/homeStore';
import { Avatar, StatusPill } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';
import { WorkspaceButton } from '../workspace/WorkspaceButton';
import { GuideButton } from './GuideButton';
import { NewConversationButton } from './NewConversationButton';
import { ThemeButton } from './ThemeButton';

/** "Receptionist · WhatsApp, Google Calendar". */
function subtitleOf(employee: EmployeeSummary): string {
  const apps = employee.apps.map((app) => app.name).join(', ');
  return [employee.role, apps].filter(Boolean).join(' · ');
}

function PhotoButton({ employee }: { employee: EmployeeSummary }) {
  const setPhoto = useSetEmployeePhoto();
  const input = useRef<HTMLInputElement>(null);
  const avatar = <Avatar name={employee.name} colorRole={employee.color_role} photo={employee.photo_url} size="sm" />;
  if (employee.derived) return avatar;
  const change = (file: File | null) =>
    setPhoto.mutate(
      { workflowId: employee.workflow_id, file },
      { onError: (error) => pillToast(error.message || 'That photo couldn’t be used. Try another.', { tone: 'error' }) },
    );
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="quiet"
            size="icon"
            aria-label={`Change ${employee.name}’s photo`}
            title="Change photo"
            className="size-auto rounded-full p-0"
          >
            {avatar}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => input.current?.click()}>
            <ImagePlus />
            Upload photo…
          </DropdownMenuItem>
          {employee.photo_url && (
            <DropdownMenuItem onSelect={() => change(null)}>
              <Trash2 />
              Remove photo
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <input
        ref={input}
        type="file"
        accept={PHOTO_TYPES.join(',')}
        hidden
        data-testid="employee-photo-input"
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          event.target.value = '';
          if (file) change(file);
        }}
      />
    </>
  );
}

function Name({ employee }: { employee: EmployeeSummary }) {
  const rename = useRenameEmployee();
  const [draft, setDraft] = useState<string | null>(null);
  // The new name shows while it saves; the refreshed summary takes over.
  const shown = rename.isPending && rename.variables ? rename.variables.name : employee.name;
  const save = () => {
    if (draft === null) return;
    const name = draft.replace(/\s+/g, ' ').trim();
    setDraft(null);
    if (!name || name === employee.name) return;
    rename.mutate(
      { workflowId: employee.workflow_id, name },
      { onError: () => pillToast(`${employee.name} wasn’t renamed. Try again.`, { tone: 'error' }) },
    );
  };
  if (draft !== null) {
    return (
      <Input
        autoFocus
        aria-label="Name"
        value={draft}
        maxLength={HIRE_LIMITS.name}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            save();
          } else if (event.key === 'Escape') {
            event.preventDefault();
            setDraft(null);
          }
        }}
        onBlur={save}
        className="h-7 w-48 px-2 text-base font-semibold"
      />
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-0.5">
      <h2 className="m-0 truncate text-base font-semibold text-fg-default">{shown}</h2>
      <Button
        variant="quiet"
        size="icon"
        aria-label={`Rename ${shown}`}
        title="Rename"
        onClick={() => setDraft(shown)}
        className="size-6 shrink-0 rounded-md text-fg-faint"
      >
        <Pencil className="size-3.5" strokeWidth={1.75} />
      </Button>
    </div>
  );
}

function Identity({ employee }: { employee: EmployeeSummary }) {
  const pending = useWorkflowControlPending(employee.workflow_id);
  const answering = useAnswering(employee);
  const { pill, pulse } = presentEmployee(employee, pending, answering);
  const subtitle = subtitleOf(employee);
  return (
    <div className="flex min-w-0 items-center gap-2.5 px-1">
      <PhotoButton employee={employee} />
      <div className="flex min-w-0 flex-col">
        <Name employee={employee} />
        {subtitle && <span className="truncate text-xs text-fg-muted">{subtitle}</span>}
      </div>
      <StatusPill tone={pill.tone} label={pill.label} pulse={pulse} size="compact" />
    </div>
  );
}

export function HomeHeader({ title, employee, scrolled }: { title: string; employee: EmployeeSummary | null; scrolled: boolean }) {
  const sidebarOpen = useHomeStore((s) => s.sidebarOpen);
  const toggleSidebar = useHomeStore((s) => s.toggleSidebar);
  const logoPulse = useHomeStore((s) => s.logoPulse);
  const employeeId = useHomeStore((s) => (s.view.kind === 'employee' ? s.view.workflowId : undefined));

  return (
    <header
      className={cn(
        'relative z-10 flex h-(--h-home-header) shrink-0 items-center gap-2 border-b px-3.5 transition-colors duration-(--dur-slow)',
        scrolled ? 'border-border-default' : 'border-transparent',
      )}
    >
      {!sidebarOpen && (
        <>
          <Button variant="quiet" size="icon" onClick={toggleSidebar} aria-label="Open sidebar" title="Open sidebar" className="rounded-lg">
            <PanelLeft className="size-4.25" strokeWidth={1.75} />
          </Button>
          <OcLogo size="header" pulseNonce={logoPulse} className="pr-1.5 pl-0.5" />
          <span aria-hidden className="h-5 w-px bg-border-default" />
        </>
      )}
      {employee ? <Identity employee={employee} /> : <h2 className="truncate px-1.5 text-lead font-semibold text-fg-default">{title}</h2>}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {employee && employee.talk.state === 'on' && <NewConversationButton employee={employee} />}
        <GuideButton />
        <WorkspaceButton />
        <ModeToggle workflowId={employeeId} />
        <ThemeButton />
      </div>
    </header>
  );
}

export default HomeHeader;
