/**
 * The twelve components an employee can show in a chat reply (design
 * handoff chat, catalog.json), as json-render draws them. registry.ts wraps
 * each in the guard (lib/jsonRender/guard.tsx), which reads the element's
 * props through the catalogue's forgiving schema first, so these get props
 * already parsed, plus the ref that plays their entrance.
 *
 * Controls write through json-render's bindings: SlotPicker's, Select's and
 * TextField's `value`, Toggle's `checked`. A Button presses its `on.press`
 * binding (emit), which json-render resolves against the UI's state and
 * hands to the chat's action handlers (genui/actions.ts).
 *
 * Surfaces follow the handoff's token map: panels on `--bg-panel` with the
 * default border, the picked option in the tools tint, a primary button in
 * the run tint, a callout's tone in the save / config / stop / run tints.
 */

import { useBoundProp } from '@json-render/react';
import { useId } from 'react';
import { Check, CircleAlert, Info, TriangleAlert } from 'lucide-react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import type { GuardedComponentProps } from '@/lib/jsonRender';
import { cn } from '@/lib/utils';
import type { ChatComponentType, ChatPropsOf } from './catalog';

type ViewProps<T extends ChatComponentType> = GuardedComponentProps<ChatPropsOf<T>>;

const GAP = { sm: 'gap-2', md: 'gap-3' } as const;

const CALLOUT = {
  info: { surface: 'border-action-save-border bg-action-save-soft', ink: 'text-action-save-ink', Icon: Info },
  warning: { surface: 'border-action-config-border bg-action-config-soft', ink: 'text-action-config-ink', Icon: TriangleAlert },
  danger: { surface: 'border-action-stop-border bg-action-stop-soft', ink: 'text-action-stop-ink', Icon: CircleAlert },
  success: { surface: 'border-action-run-border bg-action-run-soft', ink: 'text-action-run-ink', Icon: Check },
} as const;

const PANEL = 'rounded-xl border border-border-default bg-bg-panel';

// ----- layout -----

export function StackView({ props, children, enterRef }: ViewProps<'Stack'>) {
  return (
    <div ref={enterRef} className={cn('flex min-w-0 flex-col', GAP[props.gap])}>
      {children}
    </div>
  );
}

export function RowView({ props, children, enterRef }: ViewProps<'Row'>) {
  return (
    <div ref={enterRef} className={cn('flex min-w-0 flex-wrap items-center', GAP[props.gap])}>
      {children}
    </div>
  );
}

export function CardView({ props, children, enterRef }: ViewProps<'Card'>) {
  return (
    <section ref={enterRef} aria-label={props.title || undefined} className={cn(PANEL, 'flex min-w-0 flex-col gap-3.5 px-4 py-3.5')}>
      {(props.title || props.description) && (
        <header className="flex flex-col gap-0.5">
          {props.title && <h3 className="m-0 text-sm font-semibold text-fg-default">{props.title}</h3>}
          {props.description && <p className="m-0 text-xs text-fg-muted">{props.description}</p>}
        </header>
      )}
      {children}
    </section>
  );
}

// ----- inputs -----

export function SlotPickerView({ props, bindings, enterRef }: ViewProps<'SlotPicker'>) {
  const [, setValue] = useBoundProp<string>(props.value, bindings?.value);
  if (props.options.length === 0) return null;
  return (
    <div ref={enterRef} role="radiogroup" aria-label={props.label} className={cn(PANEL, 'flex min-w-0 flex-col gap-2.5 p-3.5')}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-sm font-semibold text-fg-default">{props.label}</span>
        {props.hint && <span className="text-xs text-fg-muted">{props.hint}</span>}
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(8rem,1fr))] gap-2">
        {props.options.map((option) => {
          const picked = option.id === props.value;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={picked}
              disabled={option.unavailable}
              onClick={() => setValue(option.id)}
              className={cn(
                'relative flex min-w-0 flex-col items-start gap-0.5 rounded-xl border px-3 py-2.5 text-left transition-colors duration-(--dur-default)',
                picked
                  ? 'border-action-tools-border bg-action-tools-soft text-action-tools-ink'
                  : 'border-border-default bg-bg-panel text-fg-default hover:border-border-strong hover:bg-bg-hover',
                option.unavailable && 'cursor-not-allowed opacity-55 hover:border-border-default hover:bg-bg-panel',
              )}
            >
              <span className="font-mono text-sm font-semibold">{option.time}</span>
              {option.detail && <span className={cn('text-xs', picked ? 'text-action-tools-ink' : 'text-fg-muted')}>{option.detail}</span>}
              {option.note && <span className="text-2xs text-fg-faint">{option.note}</span>}
              {option.recommended && !option.unavailable && (
                <span className="absolute top-2 right-2 rounded-pill border border-action-run-border bg-action-run-soft px-1.5 font-mono text-2xs text-action-run-ink">
                  Best
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function SelectView({ props, bindings, enterRef }: ViewProps<'Select'>) {
  const [, setValue] = useBoundProp<string>(props.value, bindings?.value);
  if (props.options.length === 0) return null;
  return (
    <div ref={enterRef} className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-muted">{props.label}</span>
      <ToggleGroup
        type="single"
        variant="chips"
        aria-label={props.label}
        value={props.value ?? ''}
        onValueChange={(next) => next && setValue(next)}
        className="self-start"
      >
        {props.options.map((option) => (
          <ToggleGroupItem key={option} value={option}>
            {option}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

export function ToggleView({ props, bindings, enterRef }: ViewProps<'Toggle'>) {
  const [, setChecked] = useBoundProp<boolean>(props.checked, bindings?.checked);
  const id = useId();
  const hintId = `${id}-hint`;
  // The whole row is the switch's label, so a click anywhere on it toggles.
  return (
    <Label ref={enterRef} htmlFor={id} className="flex cursor-pointer items-center gap-3 leading-normal font-normal">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm font-medium text-fg-default">{props.label}</span>
        {props.hint && (
          <span id={hintId} className="text-xs text-fg-muted">
            {props.hint}
          </span>
        )}
      </span>
      <Switch
        id={id}
        size="md"
        tone="run"
        aria-label={props.label}
        aria-describedby={props.hint ? hintId : undefined}
        checked={props.checked}
        onCheckedChange={setChecked}
      />
    </Label>
  );
}

export function TextFieldView({ props, bindings, enterRef }: ViewProps<'TextField'>) {
  const [, setValue] = useBoundProp<string>(props.value, bindings?.value);
  return (
    <label ref={enterRef} className="flex min-w-0 flex-col gap-1.5 text-xs font-medium text-fg-muted">
      {props.label}
      <Input
        value={props.value}
        placeholder={props.placeholder}
        maxLength={400}
        disabled={!bindings?.value}
        onChange={(event) => setValue(event.target.value)}
        className="h-9 rounded-lg bg-bg-input px-3 text-sm font-normal text-fg-default"
      />
    </label>
  );
}

// ----- display -----

export function TextView({ props, enterRef }: ViewProps<'Text'>) {
  if (!props.text) return null;
  return (
    <p ref={enterRef} className="m-0 text-sm leading-relaxed text-fg-muted">
      {props.text}
    </p>
  );
}

export function StatGridView({ props, enterRef }: ViewProps<'StatGrid'>) {
  if (props.items.length === 0) return null;
  return (
    <div ref={enterRef} className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-2">
      {props.items.map((item, index) => (
        <div key={`${item.label}-${index}`} className={cn(PANEL, 'flex min-w-0 flex-col gap-1 px-3.5 py-3')}>
          <span className="font-mono text-2xs tracking-label text-fg-faint uppercase">{item.label}</span>
          <span className="text-xl font-semibold tracking-hero text-fg-default tabular-nums">{item.value}</span>
          {item.delta && (
            <span
              className={cn(
                'text-xs font-medium',
                item.tone === 'up' ? 'text-action-run-ink' : item.tone === 'down' ? 'text-action-stop-ink' : 'text-fg-muted',
              )}
            >
              {item.delta}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function BarChartView({ props, enterRef }: ViewProps<'BarChart'>) {
  if (props.bars.length === 0) return null;
  const most = Math.max(...props.bars.map((item) => item.value), 0);
  return (
    <figure ref={enterRef} className={cn(PANEL, 'm-0 flex min-w-0 flex-col gap-2.5 px-4 py-3.5')}>
      {props.title && <figcaption className="text-sm font-semibold text-fg-default">{props.title}</figcaption>}
      <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
        {props.bars.map((item, index) => (
          <li key={`${item.label}-${index}`} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_2.5rem] items-center gap-2.5">
            <span className="truncate text-xs text-fg-muted">{item.label}</span>
            <span className="h-2 overflow-hidden rounded-pill bg-bg-active">
              <span
                className="opencompany-grow block h-full rounded-[inherit] bg-action-tools-ink"
                // The bar's length is the value itself.
                style={{ width: `${most > 0 ? (item.value / most) * 100 : 0}%`, animationDelay: `${index * 60}ms` }}
              />
            </span>
            <span className="text-right font-mono text-xs font-medium text-fg-default">{item.value}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function CalloutView({ props, enterRef }: ViewProps<'Callout'>) {
  if (!props.text) return null;
  const tone = CALLOUT[props.tone];
  return (
    <div ref={enterRef} role="note" className={cn('flex gap-2.5 rounded-xl border px-3.5 py-3 text-sm leading-normal', tone.surface)}>
      <tone.Icon aria-hidden className={cn('mt-0.5 size-3.75 shrink-0', tone.ink)} strokeWidth={2} />
      <span className="text-fg-default">{props.text}</span>
    </div>
  );
}

// ----- actions -----

export function ButtonView({ props, emit, enterRef }: ViewProps<'Button'>) {
  if (!props.label) return null;
  const press = () => emit('press');
  if (props.variant === 'primary') {
    return (
      <ActionButton ref={enterRef} intent="run" onClick={press} className="h-8.5 rounded-lg px-3.5 whitespace-nowrap active:translate-y-px">
        {props.label}
      </ActionButton>
    );
  }
  return (
    <Button
      ref={enterRef}
      variant="quiet"
      onClick={press}
      className="h-8.5 rounded-lg border-border-default px-3.5 font-semibold text-fg-default hover:bg-bg-hover"
    >
      {props.label}
    </Button>
  );
}
