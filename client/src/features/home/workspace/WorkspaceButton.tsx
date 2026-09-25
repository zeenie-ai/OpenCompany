/**
 * The header's Workspace pill (design handoff "Workspace panel"): opens and
 * closes the dock. Tinted purple while the dock is open; while it is
 * closed, a blinking dot says someone on the team is working.
 */

import { Monitor } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useEmployeesQuery } from '../data/employees';
import { useHomeStore } from '../state/homeStore';
import { StatusDot } from '../ui/primitives';

export function WorkspaceButton() {
  const open = useHomeStore((s) => s.workspaceOpen);
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const closeWorkspace = useHomeStore((s) => s.closeWorkspace);
  const { data: team } = useEmployeesQuery();
  const someoneWorking = !open && (team ?? []).some((employee) => employee.status === 'working');

  return (
    <Button
      variant="quiet"
      aria-pressed={open}
      title="Workspace: see what your employees are working on"
      onClick={() => (open ? closeWorkspace() : openWorkspace())}
      className={cn(
        'h-8.5 gap-1.75 rounded-pill px-3 text-sm',
        // Open keeps its purple on hover, where the quiet variant would grey it.
        open
          ? 'border-node-agent-border bg-node-agent-soft text-node-agent-ink hover:bg-node-agent-hover hover:text-node-agent-ink'
          : 'border-border-default text-fg-default hover:border-border-strong',
      )}
    >
      <Monitor aria-hidden className="size-3.75" strokeWidth={1.75} />
      Workspace
      {someoneWorking && <StatusDot tone="live" />}
    </Button>
  );
}

export default WorkspaceButton;
