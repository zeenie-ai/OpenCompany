/**
 * The header's Workspace button (chat/V2.md's prototype, the chat header):
 * opens and closes the dock. Borderless like New conversation, filled while
 * the dock is open; while it is closed, a red blinking dot says the employee
 * the dock would show is live (workspaceStatus), unless the owner has their
 * screen.
 */

import { PanelRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useEmployeesQuery } from '../data/employees';
import { workspaceStatus } from '../data/presentation';
import { dockEmployee, useHomeStore } from '../state/homeStore';
import { StatusDot } from '../ui/primitives';

export function WorkspaceButton() {
  const open = useHomeStore((s) => s.workspaceOpen);
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const closeWorkspace = useHomeStore((s) => s.closeWorkspace);
  const workspaceFor = useHomeStore((s) => s.workspaceFor);
  const { data: team } = useEmployeesQuery();
  const employee = dockEmployee(team, workspaceFor);
  const takenOver = useHomeStore((s) => employee !== null && employee.workflow_id in s.takeovers);
  const live = !open && employee !== null && workspaceStatus(employee, takenOver).tone === 'live';

  return (
    <Button
      variant="quiet"
      aria-pressed={open}
      title="Workspace: see what your employees are working on"
      onClick={() => (open ? closeWorkspace() : openWorkspace())}
      className="h-8 gap-1.75 rounded-lg px-2.5 text-sm aria-pressed:bg-bg-active aria-pressed:text-fg-default"
    >
      <PanelRight aria-hidden className="size-4" strokeWidth={1.75} />
      <span data-label>Workspace</span>
      {live && <StatusDot tone="live" className="size-1.5" />}
    </Button>
  );
}

export default WorkspaceButton;
