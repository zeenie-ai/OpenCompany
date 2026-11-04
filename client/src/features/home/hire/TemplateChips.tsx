/**
 * Starter jobs under the composer (design handoff "Template chips"). A chip
 * puts its job in the composer for the owner to read, change and send;
 * while the composer still holds that job as written, the chip shows as
 * picked and, given `onHireNow`, offers to hire the starter as it stands
 * ("Hire now"). The Welcome guide shows the chips alone. The chips and
 * Hire now are neutral (onboarding v3): Home colours only state and role,
 * and `run` means Start.
 */

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { HIRE_TEMPLATES, type HireTemplate } from './templates';

/** Muted at rest, the foreground on hover, a raised surface with a
 *  foreground border when picked. The chip variant's lift and hover fill
 *  are cancelled. */
const CHIP =
  'text-fg-muted hover:translate-y-0 hover:border-border-strong hover:bg-transparent hover:text-fg-default aria-pressed:border-fg-default aria-pressed:bg-bg-elevated aria-pressed:text-fg-default';

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
            className={CHIP}
          >
            {template.label}
          </Button>
        ))}
      </div>
      {picked && onHireNow && (
        <div className="mt-3 flex max-w-(--w-composer) flex-wrap items-center justify-center gap-x-3 gap-y-2 text-center">
          <span className="text-sm text-pretty text-fg-muted">{picked.summary}</span>
          <Button
            variant="quiet"
            disabled={disabled}
            onClick={() => onHireNow(picked)}
            className="rounded-pill border-border-default px-3.5 font-semibold text-fg-default"
          >
            Hire now
          </Button>
        </div>
      )}
    </>
  );
}

export default TemplateChips;
