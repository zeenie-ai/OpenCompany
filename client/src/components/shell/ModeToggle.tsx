/**
 * The Normal / Dev switch, shared by the editor toolbar and the Home header
 * (design handoff "Mode toggle"). A neutral segmented control: the mode on
 * show is the raised segment in the foreground colour, the other is muted,
 * since colour marks state or role and a mode is neither. Switching goes
 * through the shell actions, so the unsaved-work guard and the transition
 * always run. `workflowId`: the workflow Dev opens (Home passes the
 * employee on screen); without one the shell resolves Home's visible employee
 * or Workspace, then falls back to what the editor last had.
 */

import { Clock, Zap } from 'lucide-react';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useShellMode } from '../../app/ShellModeSwitch';
import { enterDev, enterNormal } from '../../app/useShellActions';

// The picked segment: a raised surface with a hairline, in both themes.
const ITEM = 'data-[state=on]:border-border-default data-[state=on]:shadow-card';

export function ModeToggle({ className, workflowId }: { className?: string; workflowId?: string }) {
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
        else if (next === 'dev') void enterDev({ workflowId });
      }}
      className={className}
    >
      <ToggleGroupItem value="normal" className={ITEM}>
        <Clock aria-hidden />
        <span data-label>Normal</span>
      </ToggleGroupItem>
      <ToggleGroupItem value="dev" className={ITEM}>
        <Zap aria-hidden />
        <span data-label>Dev</span>
      </ToggleGroupItem>
    </ToggleGroup>
  );
}

export default ModeToggle;
