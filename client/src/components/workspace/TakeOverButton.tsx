import type { ComponentProps } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/** Take over (and, where a screen holds it itself, Hand back), as every
 *  Workspace screen shows it: a neutral pill beside that screen's controls.
 *  Colour marks a state or a role, and this is neither. */
export function TakeOverButton({ className, children = 'Take over', ...props }: ComponentProps<typeof Button>) {
  return (
    <Button
      variant="quiet"
      {...props}
      className={cn('h-8.5 shrink-0 rounded-pill border-border-default bg-bg-elevated px-3.5 text-sm font-medium text-fg-default', className)}
    >
      {children}
    </Button>
  );
}
