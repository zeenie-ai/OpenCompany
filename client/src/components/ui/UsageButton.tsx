/**
 * Usage, the same button in Home's header (on an employee's page) and in the
 * editor's top toolbar (for the open workflow): it opens what the employee
 * cost this month, the model tokens it used and the tasks it finished
 * (`EmployeeUsage`, the component an employee's row in the cost panel opens
 * to as well).
 *
 * Modal, like the toolbar's model menu: the editor's canvas stops the
 * mousedown a press outside would close it with, so a press there left a
 * plain popover open. Modal, any press closes it, in both modes.
 */

import type { ComponentProps } from 'react';
import { ChartNoAxesColumn } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmployeeUsage } from '@/components/ui/EmployeeUsage';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';

type UsageButtonProps = { workflowId: string; name: string } & Pick<ComponentProps<typeof Button>, 'size' | 'className'>;

/** Home's look by default; the editor's toolbar passes its own. */
export function UsageButton({ workflowId, name, size, className = 'h-8 gap-1.75 rounded-lg px-2.5 text-sm' }: UsageButtonProps) {
  return (
    <Popover modal>
      <PopoverTrigger asChild>
        <Button variant="quiet" size={size} title="Usage this month" className={className}>
          <ChartNoAxesColumn aria-hidden className="size-4" strokeWidth={1.75} />
          <span data-label>Usage</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" aria-label={`${name}’s usage this month`} className="flex w-80 flex-col gap-3 p-4">
        <h3 className="m-0 truncate text-sm font-semibold text-fg-default">{name} this month</h3>
        <EmployeeUsage workflowId={workflowId} />
      </PopoverContent>
    </Popover>
  );
}

export default UsageButton;
