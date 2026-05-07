/**
 * ActionButton -- colored "soft" toolbar button shared by the parameter
 * panel, location panel, settings header, and top toolbar.
 *
 * The `intent` prop is a semantic role (run / stop / save / ...), not a
 * palette color, so themes can re-skin without touching call sites.
 * Each intent reads the matching --action-X / --action-X-soft /
 * --action-X-border CSS triplet defined in index.css; pressed +
 * disabled state are baked into the variant so call sites never do
 * opacity arithmetic.
 *
 * Adding a new intent: add the --action-NAME triplet to index.css,
 * expose the three Tailwind tokens in the @theme inline block, then
 * add a case to `actionButtonVariants` below.
 */

import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { Sounds } from '@/lib/sound';

export const actionButtonVariants = cva(
  // Base: 32px tall pill with icon-text gap, semibold, focus ring, smooth hover.
  // `action-btn` + `btn` co-classes are the design-handoff structural
  // hooks for per-theme decorations (gold-foil on Renaissance, neon
  // outline on Cyber, hard 4px shadow on Atomic) and the global hover
  // sound delegate. Disabled state uses shadcn-idiomatic
  // `disabled:opacity-50` so we don't do per-token opacity arithmetic
  // at the call site.
  'action-btn btn inline-flex h-8 items-center gap-1.5 rounded-md border px-3.5 text-[13px] font-semibold transition-all outline-none select-none disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-ring/40',
  {
    variants: {
      intent: {
        run:
          'border-action-run-border bg-action-run-soft text-action-run hover:bg-action-run-hover',
        stop:
          'border-action-stop-border bg-action-stop-soft text-action-stop hover:bg-action-stop-hover',
        save:
          'border-action-save-border bg-action-save-soft text-action-save hover:bg-action-save-hover',
        config:
          'border-action-config-border bg-action-config-soft text-action-config hover:bg-action-config-hover',
        secret:
          'border-action-secret-border bg-action-secret-soft text-action-secret hover:bg-action-secret-hover',
        tools:
          'border-action-tools-border bg-action-tools-soft text-action-tools hover:bg-action-tools-hover',
      },
    },
    defaultVariants: { intent: 'save' },
  },
);

export type ActionButtonIntent = NonNullable<VariantProps<typeof actionButtonVariants>['intent']>;

export interface ActionButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof actionButtonVariants> {}

export const ActionButton = React.forwardRef<HTMLButtonElement, ActionButtonProps>(
  ({ className, intent, onClick, ...props }, ref) => {
    // Fire the per-theme `click` sound BEFORE the user-supplied
    // handler so the audio cue doesn't depend on the action
    // succeeding (e.g., even a disabled-late workflow run still
    // gives feedback). Sounds.play() is a no-op when the engine is
    // disabled or the active pack is `none`, so this costs nothing
    // in the default state.
    const handleClick = onClick
      ? (event: React.MouseEvent<HTMLButtonElement>) => {
          Sounds.play('click');
          onClick(event);
        }
      : (_event: React.MouseEvent<HTMLButtonElement>) => {
          Sounds.play('click');
        };

    return (
      <button
        ref={ref}
        type={props.type ?? 'button'}
        className={cn(actionButtonVariants({ intent }), className)}
        onClick={handleClick}
        {...props}
      />
    );
  },
);
ActionButton.displayName = 'ActionButton';
