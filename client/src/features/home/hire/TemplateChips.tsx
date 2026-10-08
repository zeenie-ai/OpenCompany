/**
 * Starter jobs under the composer (design handoff "Template chips"). A chip
 * puts its job in the composer for the owner to read, change and send;
 * while the composer still holds that job as written, the chip shows as
 * picked and, given `onHireNow`, offers to hire the starter as it stands
 * ("Hire now"). The Welcome guide shows the chips alone. Coloured by node
 * role, the same palette the employee avatars use.
 */

import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ColorRole } from '../data/schemas';
import { HIRE_TEMPLATES, type HireTemplate } from './templates';

/** Soft role tint at rest, the action-strength tint on hover and when picked. */
const CHIP_TONE: Record<ColorRole, { chip: string; dot: string }> = {
  agent: {
    chip: 'border-node-agent-border bg-node-agent-soft hover:border-node-agent-edge hover:bg-node-agent-fill aria-pressed:border-node-agent-edge aria-pressed:bg-node-agent-fill',
    dot: 'bg-node-agent',
  },
  model: {
    chip: 'border-node-model-border bg-node-model-soft hover:border-node-model-edge hover:bg-node-model-fill aria-pressed:border-node-model-edge aria-pressed:bg-node-model-fill',
    dot: 'bg-node-model',
  },
  tool: {
    chip: 'border-node-tool-border bg-node-tool-soft hover:border-node-tool-edge hover:bg-node-tool-fill aria-pressed:border-node-tool-edge aria-pressed:bg-node-tool-fill',
    dot: 'bg-node-tool',
  },
  trigger: {
    chip: 'border-node-trigger-border bg-node-trigger-soft hover:border-node-trigger-edge hover:bg-node-trigger-fill aria-pressed:border-node-trigger-edge aria-pressed:bg-node-trigger-fill',
    dot: 'bg-node-trigger',
  },
  workflow: {
    chip: 'border-node-workflow-border bg-node-workflow-soft hover:border-node-workflow-edge hover:bg-node-workflow-fill aria-pressed:border-node-workflow-edge aria-pressed:bg-node-workflow-fill',
    dot: 'bg-node-workflow',
  },
};

export function TemplateChips({
  picked,
  onPick,
  onHireNow,
  disabled = false,
  className,
}: {
  /** The starter whose job the composer holds, untouched. */
  picked: HireTemplate | null;
  onPick: (template: HireTemplate) => void;
  /** Without it the chips only fill the composer (no "Hire now" row). */
  onHireNow?: (template: HireTemplate) => void;
  disabled?: boolean;
  /** The chip row's spacing and alignment (centred by default). */
  className?: string;
}) {
  return (
    <>
      <div data-intro className={cn('flex max-w-(--w-composer) flex-wrap justify-center gap-2', className)}>
        {HIRE_TEMPLATES.map((template) => (
          <Button
            key={template.id}
            variant="chip"
            size="chip"
            disabled={disabled}
            aria-pressed={picked?.id === template.id}
            onClick={() => onPick(template)}
            className={cn('font-medium', CHIP_TONE[template.role].chip)}
          >
            <span aria-hidden className={cn('size-1.75 rounded-full', CHIP_TONE[template.role].dot)} />
            {template.label}
          </Button>
        ))}
      </div>
      {picked && onHireNow && (
        <div className="mt-3 flex max-w-(--w-composer) flex-wrap items-center justify-center gap-x-3 gap-y-2 text-center">
          <span className="text-sm text-pretty text-fg-muted">{picked.summary}</span>
          <ActionButton intent="run" disabled={disabled} onClick={() => onHireNow(picked)} className="rounded-pill">
            Hire now
          </ActionButton>
        </div>
      )}
    </>
  );
}

export default TemplateChips;
