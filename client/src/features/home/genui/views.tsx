/**
 * The setup screen's 18 components, one per catalogue type, as json-render
 * draws them. registry.ts wraps each in the guard (lib/jsonRender/guard.tsx),
 * which reads the element's props through the catalogue's forgiving schema
 * first, so these get props already parsed, plus the ref that plays their
 * entrance. Private to the genui folder (lint keeps it that way), and
 * loaded only with the screen (HireScreen).
 *
 * Controls write through json-render's bindings: a Toggle's `checked`, a
 * Choice's and an Input's `value`, the Schedule's `value` and the Plan's
 * `trigger` (both `/trigger`). A Button presses its `on.press` binding
 * (emit), which json-render resolves against the screen's state and runs
 * (actions.ts).
 *
 * The card (onboarding handoff R2): the AgentCard is the identity row, the
 * Plan the routine as a timeline (ui/routine), and Ask first with the two
 * buttons sit in the footer strip, where they take its look
 * (HireSpecContext.inFooter). When they work is the routine's When row;
 * its Change opens the editor under it, offering only what the hire can
 * build: the owner messaging them, a schedule at the times it can run, or a
 * new message in one of the apps the server says can start the work. Once
 * the owner changes it, the When row says the new trigger. Without a Plan,
 * a Schedule draws that one row.
 */

import { Fragment, useContext, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import { useBoundProp, useStateValue } from '@json-render/react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { LiveUiContext, type GuardedComponentProps } from '@/lib/jsonRender';
import { animate, RISE, stagger } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { Avatar, MicroLabel } from '../ui/primitives';
import { RoutineRow, RoutineTimeline } from '../ui/routine';
import { STATE_PATHS, type PROP_SCHEMAS, type PropsOf, type StepRole, type Tone } from './catalog';
import { getPath } from './expressions';
import { HireSpecContext } from './hireSpecContext';
import {
  LAST_MONTH_DAY,
  SCHEDULE_EVERY,
  SCHEDULE_TIMES,
  WEEKDAYS,
  routineSteps,
  snapTrigger,
  triggerSentence,
  type HireTrigger,
} from './hirePayload';
import { MessagePreview } from './MessagePreview';

type ViewProps<T extends keyof typeof PROP_SCHEMAS> = GuardedComponentProps<PropsOf<T>>;

// ----- token maps (Tailwind scans these literals) -----

const TONE_CARD: Record<Tone, string> = {
  agent: 'border-node-agent-border bg-linear-135/srgb from-node-agent-soft to-bg-elevated to-60%',
  model: 'border-node-model-border bg-linear-135/srgb from-node-model-soft to-bg-elevated to-60%',
  tool: 'border-node-tool-border bg-linear-135/srgb from-node-tool-soft to-bg-elevated to-60%',
  trigger: 'border-node-trigger-border bg-linear-135/srgb from-node-trigger-soft to-bg-elevated to-60%',
  workflow: 'border-node-workflow-border bg-linear-135/srgb from-node-workflow-soft to-bg-elevated to-60%',
  neutral: 'border-border-default bg-bg-elevated',
};

const TONE_INK: Record<Tone, string> = {
  agent: 'text-node-agent-ink',
  model: 'text-node-model-ink',
  tool: 'text-node-tool-ink',
  trigger: 'text-node-trigger-ink',
  workflow: 'text-node-workflow-ink',
  neutral: 'text-fg-default',
};

const TONE_DOT: Record<Tone, string> = {
  agent: 'bg-node-agent',
  model: 'bg-node-model',
  tool: 'bg-node-tool',
  trigger: 'bg-node-trigger',
  workflow: 'bg-node-workflow',
  neutral: 'bg-fg-faint',
};

const TONE_BADGE: Record<Tone, string> = {
  agent: 'border-node-agent-edge bg-node-agent-fill text-node-agent-ink',
  model: 'border-node-model-edge bg-node-model-fill text-node-model-ink',
  tool: 'border-node-tool-edge bg-node-tool-fill text-node-tool-ink',
  trigger: 'border-node-trigger-edge bg-node-trigger-fill text-node-trigger-ink',
  workflow: 'border-node-workflow-edge bg-node-workflow-fill text-node-workflow-ink',
  neutral: 'border-border-default bg-bg-hover text-fg-default',
};

const EVERY_LABEL: Record<NonNullable<HireTrigger['every']>, string> = {
  hour: 'Every hour',
  day: 'Every day',
  weekday: 'Weekdays',
  week: 'Every week',
  month: 'Every month',
};

const MONTH_DAYS = Array.from({ length: LAST_MONTH_DAY }, (_, index) => String(index + 1));

const STACK_GAP = { sm: 'gap-2', md: 'gap-3', lg: 'gap-4.5' } as const;

function Pip({ className }: { className?: string }) {
  return <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', className)} />;
}

// ----- components -----

export function StackView({ props, children, enterRef }: ViewProps<'Stack'>) {
  const horizontal = props.direction === 'horizontal';
  return (
    <div
      ref={enterRef}
      className={cn('flex min-w-0', STACK_GAP[props.gap], horizontal ? 'flex-row flex-wrap items-center' : 'flex-col')}
    >
      {children}
    </div>
  );
}

export function GridView({ props, children, enterRef }: ViewProps<'Grid'>) {
  return (
    <div
      ref={enterRef}
      className={cn(
        'grid gap-2.5',
        props.columns === 3 ? 'grid-cols-[repeat(auto-fit,minmax(150px,1fr))]' : 'grid-cols-[repeat(auto-fit,minmax(210px,1fr))]',
      )}
    >
      {children}
    </div>
  );
}

/** A compact section of the card: the model's own rules, choices and
 *  notes, kept in view so nothing is hired unseen. */
export function CardView({ props, children, enterRef }: ViewProps<'Card'>) {
  return (
    <div ref={enterRef} className={cn('flex flex-col gap-2.5 rounded-row border px-3.5 py-3', TONE_CARD[props.tone ?? 'neutral'])}>
      {(props.title || props.subtitle) && (
        <div className="flex flex-col gap-0.5">
          {props.title && <MicroLabel>{props.title}</MicroLabel>}
          {props.subtitle && <span className="text-sm text-fg-muted">{props.subtitle}</span>}
        </div>
      )}
      {children}
    </div>
  );
}

export function HeadingView({ props, enterRef }: ViewProps<'Heading'>) {
  return (
    <h3 ref={enterRef} className="m-0 text-md font-semibold tracking-[-0.01em] text-fg-default">
      {props.text}
    </h3>
  );
}

export function TextView({ props, enterRef }: ViewProps<'Text'>) {
  return (
    <p ref={enterRef} className={cn('m-0 text-base leading-relaxed text-pretty', props.muted ? 'text-fg-muted' : 'text-fg-default')}>
      {props.text}
    </p>
  );
}

export function MetricView({ props, enterRef }: ViewProps<'Metric'>) {
  return (
    <div ref={enterRef} className="flex flex-col gap-1 rounded-card border border-border-default bg-bg-elevated p-3.5">
      <span className="text-xs text-fg-muted">{props.label}</span>
      <span className={cn('text-xl font-semibold tracking-[-0.02em]', TONE_INK[props.tone ?? 'neutral'])}>{props.value}</span>
      {props.hint && <span className="text-xs text-fg-faint">{props.hint}</span>}
    </div>
  );
}

export function BadgeView({ props, enterRef }: ViewProps<'Badge'>) {
  return (
    <span
      ref={enterRef}
      className={cn(
        'inline-flex h-5.5 items-center self-start rounded-pill border px-2.25 text-2xs font-medium whitespace-nowrap',
        TONE_BADGE[props.tone ?? 'neutral'],
      )}
    >
      {props.label}
    </span>
  );
}

export function DividerView({ enterRef }: ViewProps<'Divider'>) {
  return <div ref={enterRef} className="h-px bg-border-default" />;
}

/** The app's own name, when the reply's name for it is one that can start the work. */
function appNameOf(trigger: HireTrigger, triggerApps: Readonly<Record<string, string>>): string | undefined {
  return trigger.app ? (triggerApps[trigger.app.toLowerCase()] ?? trigger.app) : undefined;
}

interface RoutineStepProps {
  title: string;
  detail?: string;
  role: StepRole;
}

/** The routine as a timeline. The When row (the first trigger step) ends in
 *  Change while `onChange` is given, which opens the editor under it. Rows
 *  rise in one after another when the screen arrives live: that is the
 *  routine's entrance, so the element's own is not played. */
function Routine({
  steps,
  trigger,
  appName,
  onChange,
}: {
  steps: readonly RoutineStepProps[];
  trigger: HireTrigger;
  appName: string | undefined;
  /** Null when nothing is bound to /trigger: no Change. */
  onChange: ((next: HireTrigger) => void) | null;
}) {
  const live = useContext(LiveUiContext);
  const rowsRef = useRef<HTMLDivElement>(null);
  const [editing, setEditing] = useState(false);
  const whenAt = steps.findIndex((step) => step.role === 'trigger');

  useLayoutEffect(() => {
    const rows = rowsRef.current?.querySelectorAll('[data-routine-row]');
    if (live && rows) stagger(rows, RISE, { base: 120, step: 90, duration: 'follow-in', easing: 'spring' });
  }, [live]);

  return (
    <div className="min-w-0">
      <RoutineTimeline ref={rowsRef}>
        {steps.map((step, index) => (
          <Fragment key={index}>
            <RoutineRow
              role={step.role}
              title={step.title}
              detail={step.detail}
              action={
                index === whenAt && onChange ? (
                  <Button
                    variant="quiet"
                    aria-expanded={editing}
                    onClick={() => setEditing((on) => !on)}
                    className="h-6.5 rounded-pill border-border-default px-2.5 text-xs"
                  >
                    {editing ? 'Done' : 'Change'}
                  </Button>
                ) : undefined
              }
            />
            {index === whenAt && onChange && editing && <ScheduleEditor trigger={trigger} appName={appName} onChange={onChange} />}
          </Fragment>
        ))}
      </RoutineTimeline>
    </div>
  );
}

export function PlanView({ props, bindings }: ViewProps<'Plan'>) {
  const { writtenState, triggerApps } = useContext(HireSpecContext);
  const [, setTrigger] = useBoundProp<unknown>(props.trigger, bindings?.trigger);
  const current = snapTrigger(useStateValue<unknown>(STATE_PATHS.trigger));
  const written = snapTrigger(getPath(writtenState, STATE_PATHS.trigger));
  const appName = appNameOf(current, triggerApps);
  const steps = routineSteps(props.steps, written, current, appName);
  // A routine with no "When" step still says when they work, first.
  const shown = steps.some((step) => step.role === 'trigger')
    ? steps
    : [{ title: triggerSentence(current, appName), role: 'trigger' as const }, ...steps];
  return (
    <Routine
      steps={shown}
      trigger={current}
      appName={appName}
      onChange={bindings?.trigger ? (next) => setTrigger(snapTrigger(next)) : null}
    />
  );
}

function ScheduleField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      {children}
    </div>
  );
}

function SchedulePicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="min-w-28 rounded-lg bg-bg-app text-fg-default dark:bg-bg-app">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** When they work, edited: what starts the work, and for a schedule how
 *  often, on which day and at what time. Opens under the routine's When
 *  row, in line with its text. */
function ScheduleEditor({
  trigger,
  appName,
  onChange,
}: {
  trigger: HireTrigger;
  appName: string | undefined;
  onChange: (next: HireTrigger) => void;
}) {
  const { triggerApps } = useContext(HireSpecContext);
  const apps = [...new Set(Object.values(triggerApps))];
  const change = (next: HireTrigger) => onChange(snapTrigger(next));
  const starts = trigger.kind === 'app_event' ? `app:${appName ?? ''}` : trigger.kind;
  const pickStart = (value: string) => {
    if (value === 'manual' || value === 'schedule') change({ kind: value });
    else change({ kind: 'app_event', app: value.slice('app:'.length) });
  };
  return (
    <div className="mt-1 mb-2 ml-21 flex flex-col gap-3 rounded-row border border-border-default bg-bg-elevated p-3">
      <ScheduleField label="What starts their work">
        <ToggleGroup
          type="single"
          variant="chips"
          aria-label="What starts their work"
          value={starts}
          onValueChange={(next) => next && pickStart(next)}
          className="flex-wrap gap-1.5"
        >
          <ToggleGroupItem value="manual">When you message them</ToggleGroupItem>
          <ToggleGroupItem value="schedule">On a schedule</ToggleGroupItem>
          {apps.map((app) => (
            <ToggleGroupItem key={app} value={`app:${app}`}>
              When something new arrives in {app}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </ScheduleField>
      {trigger.kind === 'schedule' && (
        <>
          <ScheduleField label="How often">
            <ToggleGroup
              type="single"
              variant="segmented"
              aria-label="How often"
              value={trigger.every ?? ''}
              onValueChange={(next) => next && change({ ...trigger, every: next as HireTrigger['every'] })}
              className="flex-wrap self-start"
            >
              {SCHEDULE_EVERY.map((every) => (
                <ToggleGroupItem key={every} value={every} className="border-0">
                  {EVERY_LABEL[every]}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </ScheduleField>
          {trigger.every !== 'hour' && (
            <div className="flex flex-wrap gap-4">
              {trigger.every === 'week' && (
                <ScheduleField label="On">
                  <SchedulePicker
                    label="Day of the week"
                    value={trigger.day}
                    options={WEEKDAYS.map((day) => ({ value: day, label: `${day.charAt(0).toUpperCase()}${day.slice(1)}` }))}
                    onChange={(day) => change({ ...trigger, day })}
                  />
                </ScheduleField>
              )}
              {trigger.every === 'month' && (
                <ScheduleField label="On day">
                  <SchedulePicker
                    label="Day of the month"
                    value={trigger.day}
                    options={MONTH_DAYS.map((day) => ({ value: day, label: day }))}
                    onChange={(day) => change({ ...trigger, day })}
                  />
                </ScheduleField>
              )}
              <ScheduleField label="At">
                <SchedulePicker
                  label="Time"
                  value={trigger.at}
                  options={SCHEDULE_TIMES.map((time) => ({ value: time, label: time }))}
                  onChange={(at) => change({ ...trigger, at })}
                />
              </ScheduleField>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** When they work, for a screen without a routine: its one When row. */
export function ScheduleView({ props, bindings }: ViewProps<'Schedule'>) {
  const { triggerApps } = useContext(HireSpecContext);
  const [, setValue] = useBoundProp<unknown>(props.value, bindings?.value);
  const trigger = snapTrigger(props.value);
  const appName = appNameOf(trigger, triggerApps);
  return (
    <Routine
      steps={[{ title: triggerSentence(trigger, appName), role: 'trigger' }]}
      trigger={trigger}
      appName={appName}
      onChange={bindings?.value ? (next) => setValue(snapTrigger(next)) : null}
    />
  );
}

/** Who they are: the setup card's identity row. The description goes into
 *  their instructions, so it shows too, kept to two lines. */
export function AgentCardView({ props, enterRef }: ViewProps<'AgentCard'>) {
  const sub = [props.role, props.apps.join(', ')].filter(Boolean).join(' · ');
  return (
    <div ref={enterRef} className="flex min-w-0 items-center gap-3.5">
      <Avatar name={props.name} colorRole="agent" size="card" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-lg leading-tight font-semibold tracking-[-0.02em] text-fg-default">{props.name}</span>
        {sub && <span className="truncate text-sm text-fg-muted">{sub}</span>}
        {props.description && <span className="line-clamp-2 text-sm text-pretty text-fg-muted">{props.description}</span>}
      </div>
    </div>
  );
}

export function ListView({ props, enterRef }: ViewProps<'List'>) {
  return (
    <div ref={enterRef} className="flex flex-col rounded-card border border-border-default bg-bg-elevated p-1.5">
      {props.items.map((item, index) => (
        <div key={index} className={cn('flex items-start gap-2.5 p-2.5', index > 0 && 'border-t border-border-default')}>
          <Pip className={cn('mt-1.5', TONE_DOT[item.tone ?? 'neutral'])} />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-base font-medium text-fg-default">{item.title}</span>
            {item.detail && <span className="text-meta leading-snug text-fg-muted">{item.detail}</span>}
          </div>
          {item.meta && <span className="mt-0.5 font-mono text-2xs whitespace-nowrap text-fg-faint">{item.meta}</span>}
        </div>
      ))}
    </div>
  );
}

export function DraftView({ props, enterRef }: ViewProps<'Draft'>) {
  return (
    <div ref={enterRef}>
      <MessagePreview channel={props.channel} to={props.to} subject={props.subject} body={props.body} />
    </div>
  );
}

export function ProgressView({ props, enterRef }: ViewProps<'Progress'>) {
  const barRef = useRef<HTMLDivElement>(null);
  const shown = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (shown.current === props.value) return;
    shown.current = props.value;
    animate(barRef.current, [{ width: '0%' }, { width: `${props.value}%` }], { duration: 900, easing: 'spring', fill: 'none' });
  }, [props.value]);
  return (
    <div ref={enterRef} className="flex flex-col gap-1.5">
      <div className="flex text-sm text-fg-muted">
        {props.label}
        <span className="ml-auto font-mono text-xs">{Math.round(props.value)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-pill bg-border-default">
        {/* Width is the runtime value. */}
        <div
          ref={barRef}
          className={cn('h-full rounded-pill', TONE_DOT[!props.tone || props.tone === 'neutral' ? 'model' : props.tone])}
          style={{ width: `${props.value}%` }}
        />
      </div>
    </div>
  );
}

/** In the footer strip it is Ask first, said shortly beside its switch. */
const FOOTER_TOGGLE_LABEL = 'Ask before sending';

export function ToggleView({ props, bindings, enterRef }: ViewProps<'Toggle'>) {
  const { inFooter } = useContext(HireSpecContext);
  const [, setChecked] = useBoundProp<boolean>(props.checked, bindings?.checked);
  const id = useId();
  if (inFooter) {
    return (
      <div ref={enterRef} className="flex items-center gap-2.5">
        <Switch id={id} tone="run" checked={props.checked} onCheckedChange={setChecked} />
        <label htmlFor={id} className="cursor-pointer text-sm text-fg-muted">
          {FOOTER_TOGGLE_LABEL}
        </label>
      </div>
    );
  }
  return (
    <div ref={enterRef} className="flex items-center gap-3.5 py-0.5">
      <div className="flex flex-1 flex-col gap-0.5">
        <span className="text-row font-medium text-fg-default">{props.label}</span>
        {props.description && <span className="text-meta text-fg-muted">{props.description}</span>}
      </div>
      <Switch tone="run" aria-label={props.label} checked={props.checked} onCheckedChange={setChecked} />
    </div>
  );
}

export function ChoiceView({ props, bindings, enterRef }: ViewProps<'Choice'>) {
  const [, setValue] = useBoundProp<string>(props.value, bindings?.value);
  if (props.options.length === 0) return null;
  return (
    <div ref={enterRef} className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-muted">{props.label}</span>
      <ToggleGroup
        type="single"
        variant="segmented"
        aria-label={props.label}
        value={props.value ?? ''}
        onValueChange={(next) => next && setValue(next)}
        className="flex-wrap self-start"
      >
        {props.options.map((option) => (
          <ToggleGroupItem key={option} value={option} className="border-0">
            {option}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

export function InputView({ props, bindings, enterRef }: ViewProps<'Input'>) {
  const [, setValue] = useBoundProp<string>(props.value, bindings?.value);
  return (
    <label ref={enterRef} className="flex flex-col gap-1.5 text-xs font-medium text-fg-muted">
      {props.label}
      <Input
        value={props.value}
        placeholder={props.placeholder}
        maxLength={200}
        disabled={!bindings?.value}
        onChange={(event) => setValue(event.target.value)}
        className="h-9.5 rounded-lg bg-bg-app px-3 text-base font-normal text-fg-default md:text-base dark:bg-bg-app"
      />
    </label>
  );
}

export function ButtonView({ props, emit, enterRef }: ViewProps<'Button'>) {
  const { inFooter } = useContext(HireSpecContext);
  const press = () => emit('press');
  // The footer's pair: a quiet "Change something", then the one strong
  // control on the card, "Hire {name} →".
  if (inFooter) {
    return props.variant === 'primary' ? (
      <Button ref={enterRef} variant="invert" size="pill" onClick={press}>
        {props.label}
        <ArrowRight aria-hidden strokeWidth={2.25} />
      </Button>
    ) : (
      <Button ref={enterRef} variant="quiet" size="pill" onClick={press} className="px-3.5">
        {props.label}
      </Button>
    );
  }
  if (props.variant === 'primary') {
    return (
      <ActionButton
        ref={enterRef}
        intent="run"
        onClick={press}
        className="h-8.5 self-start rounded-lg px-4 whitespace-nowrap active:translate-y-px"
      >
        {props.label}
      </ActionButton>
    );
  }
  return (
    <Button
      ref={enterRef}
      variant="quiet"
      onClick={press}
      className="h-8.5 self-start rounded-lg border-border-strong px-3.5 font-semibold text-fg-default"
    >
      {props.label}
    </Button>
  );
}
