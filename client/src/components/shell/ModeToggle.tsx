/**
 * The Normal / Dev switch, shared by the editor toolbar and the Home header
 * (design handoff "Mode toggle"). Normal takes the skill role's green, Dev
 * the agent role's purple, each as the soft chip tint. Switching goes
 * through the shell actions, so the unsaved-work guard and the transition
 * always run.
 */

import { Clock, Zap } from 'lucide-react';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { cn } from '@/lib/utils';
import { useShellMode } from '../../app/ShellModeSwitch';
import { enterDev, enterNormal } from '../../app/useShellActions';

const ITEM = 'rounded-md data-[state=on]:shadow-none';

export function ModeToggle({ className }: { className?: string }) {
  const mode = useShellMode();
  return (
    <ToggleGroup
      type="single"
      variant="segmented"
      size="sm"
      aria-label="Mode"
      title="Ctrl/⌘ + Shift + D"
      value={mode}
      onValueChange={(next) => {
        // Radix reports '' when the pressed item is clicked again.
        if (next === 'normal') void enterNormal();
        else if (next === 'dev') void enterDev();
      }}
      className={cn('rounded-lg bg-bg-elevated p-0.5', className)}
    >
      <ToggleGroupItem
        value="normal"
        className={cn(ITEM, 'data-[state=on]:border-node-skill-border data-[state=on]:bg-node-skill-soft data-[state=on]:text-node-skill-ink')}
      >
        <Clock aria-hidden />
        Normal
      </ToggleGroupItem>
      <ToggleGroupItem
        value="dev"
        className={cn(ITEM, 'data-[state=on]:border-node-agent-border data-[state=on]:bg-node-agent-soft data-[state=on]:text-node-agent-ink')}
      >
        <Zap aria-hidden />
        Dev
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

export default ModeToggle;
