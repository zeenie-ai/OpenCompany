/**
 * Starter jobs under the composer (design handoff "Template chips"). Each
 * fills the composer with a plain-English job and sends it. Coloured by
 * node role, the same palette the employee avatars use.
 */

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ColorRole } from '../data/schemas';
import { HIRE_TEMPLATES, type HireTemplate } from './templates';

/** Soft role tint at rest, the action-strength tint on hover. */
const CHIP_TONE: Record<ColorRole, { chip: string; dot: string }> = {
  agent: {
    chip: 'border-node-agent-border bg-node-agent-soft hover:border-node-agent-edge hover:bg-node-agent-fill',
    dot: 'bg-node-agent',
  },
  model: {
    chip: 'border-node-model-border bg-node-model-soft hover:border-node-model-edge hover:bg-node-model-fill',
    dot: 'bg-node-model',
  },
  tool: {
    chip: 'border-node-tool-border bg-node-tool-soft hover:border-node-tool-edge hover:bg-node-tool-fill',
    dot: 'bg-node-tool',
  },
  trigger: {
    chip: 'border-node-trigger-border bg-node-trigger-soft hover:border-node-trigger-edge hover:bg-node-trigger-fill',
    dot: 'bg-node-trigger',
  },
  workflow: {
    chip: 'border-node-workflow-border bg-node-workflow-soft hover:border-node-workflow-edge hover:bg-node-workflow-fill',
    dot: 'bg-node-workflow',
  },
};

export function TemplateChips({ onPick, disabled = false }: { onPick: (template: HireTemplate) => void; disabled?: boolean }) {
  return (
    <div data-intro className="mt-4 flex max-w-(--w-composer) flex-wrap justify-center gap-2">
      {HIRE_TEMPLATES.map((template) => (
        <Button
          key={template.label}
          variant="chip"
          size="chip"
          disabled={disabled}
          onClick={() => onPick(template)}
          className={cn('font-medium', CHIP_TONE[template.role].chip)}
        >
          <span aria-hidden className={cn('size-1.75 rounded-full', CHIP_TONE[template.role].dot)} />
          {template.label}
        </Button>
      ))}
    </div>
  );
}

export default TemplateChips;
