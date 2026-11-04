/**
 * The Welcome step's demo (onboarding handoff A1): three beats on the left,
 * and on the right a stage drawing the one playing, a small version of what
 * the owner will see. Describe the job (the composer), Check and hire (the
 * setup card, as R2 draws it), Talk to them (a reply and a draft waiting
 * for the owner).
 *
 * It plays on its own clock, one tick every TICK_MS: the job types itself a
 * character a tick and Create pops when it is written, then the setup and
 * the conversation rise in, each beat for BEAT_TICKS[beat] ticks, round and
 * round while the Welcome step shows (it unmounts with the step). A bar
 * under the playing beat fills as it plays, and clicking a beat starts it.
 * Under reduced motion there is no clock: the conversation shows, still,
 * and a click shows another beat.
 *
 * The stage is decoration (`aria-hidden`), made of token-classed spans,
 * never real controls; the beats are the buttons.
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { RISE, animate, rise, stagger } from '@/lib/motion';
import { useReducedMotion } from '@/lib/useReducedMotion';
import { cn } from '@/lib/utils';
import { AVATAR_CLASS, STATUS_PILL_CLASS } from '../../data/presentation';
import { MicroLabel } from '../../ui/primitives';

/** What the demo's owner types. */
const DEMO_JOB = 'Answer WhatsApp messages and take bookings';

const BEATS = [{ title: 'Describe the job' }, { title: 'Check and hire' }, { title: 'Talk to them' }] as const;

/** One tick of the demo's clock, ms. */
const TICK_MS = 80;
/** How long each beat plays, in ticks: the job typed then a pause, the
 *  setup, the conversation. */
const BEAT_TICKS = [DEMO_JOB.length + 16, 48, 56] as const;

/** Where the demo is: the beat playing and how far into it. */
interface Clock {
  beat: number;
  tick: number;
}

function nextTick({ beat, tick }: Clock): Clock {
  return tick + 1 < BEAT_TICKS[beat] ? { beat, tick: tick + 1 } : { beat: (beat + 1) % BEATS.length, tick: 0 };
}

const ROUTINE = [
  { label: 'When', text: 'A customer messages you', dot: 'bg-node-trigger', ink: 'text-node-trigger-ink' },
  { label: 'They', text: 'Answer kindly and briefly', dot: 'bg-node-agent', ink: 'text-node-agent-ink' },
  { label: 'Then', text: 'Note bookings for you', dot: 'bg-node-workflow', ink: 'text-node-workflow-ink' },
] as const;

const STAGE_CARD = 'flex w-full max-w-75 flex-col border border-border-default bg-bg-panel shadow-float';

/** A small invert pill, like the composer's Create and the setup card's Hire. */
function MiniPill({ label }: { label: string }) {
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-pill bg-fg-default px-3 text-xs font-semibold whitespace-nowrap text-bg-app">
      {label}
      <ArrowRight className="size-3.5" strokeWidth={2.25} />
    </span>
  );
}

/** A stage card rising in as its beat begins. */
function useRiseIn<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    rise(ref.current);
  }, []);
  return ref;
}

/** The job typed so far; Create pops once it is all written. */
function MiniComposer({ typed }: { typed: string }) {
  const ref = useRiseIn<HTMLDivElement>();
  const pillRef = useRef<HTMLSpanElement>(null);
  const written = typed.length === DEMO_JOB.length;
  useLayoutEffect(() => {
    if (written) {
      animate(pillRef.current, [{ opacity: 0.35, transform: 'scale(0.94)' }, { opacity: 1, transform: 'none' }], {
        duration: 'chip-pop',
        easing: 'overshoot',
      });
    }
  }, [written]);
  return (
    <div ref={ref} className={cn(STAGE_CARD, 'gap-3 rounded-draft pt-3 pr-3 pb-2.5 pl-3.5')}>
      <p className="m-0 min-h-10.5 text-row leading-normal text-fg-default">
        {typed}
        <span className="opencompany-caret-blink ml-px inline-block h-3.75 w-0.5 translate-y-0.5 bg-fg-default" />
      </p>
      <div className="flex items-center gap-2">
        <span className="inline-flex h-6.5 items-center rounded-pill border border-border-default px-2.5 text-xs whitespace-nowrap text-fg-muted">
          0 apps
        </span>
        <span ref={pillRef} className={cn('ml-auto', !written && 'scale-94 opacity-35')}>
          <MiniPill label="Create" />
        </span>
      </div>
    </div>
  );
}

function MiniSetup() {
  const ref = useRiseIn<HTMLDivElement>();
  const rowsRef = useRef<HTMLDivElement>(null);
  const hireRef = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const rows = rowsRef.current?.querySelectorAll('[data-demo-row]');
    if (rows) stagger(rows, RISE, { base: 150, step: 150, duration: 'follow-in', easing: 'spring' });
    rise(hireRef.current, { delay: 650, duration: 'follow-in' });
  }, []);
  return (
    <div ref={ref} className={cn(STAGE_CARD, 'gap-3 rounded-panel p-3.5')}>
      <div className="flex items-center gap-2.5">
        <span className={cn('grid size-8.5 shrink-0 place-items-center rounded-full border-2 text-base font-semibold', AVATAR_CLASS.agent)}>
          M
        </span>
        <span className="flex min-w-0 flex-col">
          <span className="text-base font-semibold text-fg-default">Maya</span>
          <span className="truncate text-xs text-fg-muted">Receptionist · WhatsApp</span>
        </span>
      </div>
      <div ref={rowsRef} className="relative flex flex-col gap-1.5">
        <span className="absolute top-2 bottom-2 left-0.75 w-px bg-border-default" />
        {ROUTINE.map((row) => (
          <div key={row.label} data-demo-row className="relative grid grid-cols-[8px_40px_minmax(0,1fr)] items-center gap-2 text-meta">
            <span className={cn('size-1.75 rounded-full ring-3 ring-bg-panel', row.dot)} />
            <span className={cn('font-mono text-2xs tracking-label uppercase', row.ink)}>{row.label}</span>
            <span className="truncate text-fg-default">{row.text}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 border-t border-border-default pt-2.5">
        <span className="inline-flex h-4 w-7 shrink-0 items-center justify-end rounded-pill border border-action-run-border bg-action-run-hover px-0.5">
          <span className="size-2.75 rounded-full bg-action-run-ink" />
        </span>
        <span className="flex-1 text-xs text-fg-muted">Ask before sending</span>
        <span ref={hireRef}>
          <MiniPill label="Hire Maya" />
        </span>
      </div>
    </div>
  );
}

function MiniTalk({ name }: { name: string }) {
  const ref = useRiseIn<HTMLDivElement>();
  const replyRef = useRef<HTMLDivElement>(null);
  const draftRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    rise(replyRef.current, { delay: 450, duration: 'follow-in' });
    rise(draftRef.current, { delay: 950 });
  }, []);
  return (
    <div ref={ref} className="flex w-full max-w-75 flex-col gap-2.5">
      <span className="self-end rounded-card rounded-br-sm bg-bg-active px-3 py-2 text-sm text-fg-default">Hi Maya!</span>
      <div ref={replyRef} className="grid grid-cols-[26px_minmax(0,1fr)] gap-2.25">
        <span className={cn('grid size-6.5 place-items-center rounded-full border text-2xs font-semibold', AVATAR_CLASS.agent)}>M</span>
        <span className="pt-0.75 text-sm leading-normal text-fg-default">
          Hi{name ? ` ${name}` : ''}! Here’s a reply for Priya. Nothing goes out until you say so.
        </span>
      </div>
      <div ref={draftRef} className="ml-8.75 flex flex-col rounded-card border border-border-default bg-bg-elevated shadow-float">
        <div className="flex items-center gap-1.5 border-b border-border-default px-2.5 py-2">
          <MicroLabel>WhatsApp</MicroLabel>
          <span className="text-xs text-fg-muted">to Priya</span>
          <span className={cn('ml-auto inline-flex h-4.5 items-center rounded-pill border px-1.75 text-2xs font-medium whitespace-nowrap', STATUS_PILL_CLASS.waiting)}>
            Needs you
          </span>
        </div>
        <p className="m-0 px-2.5 py-2 text-xs leading-normal text-fg-default">We have a free slot on Thursday at 10:30. Shall I note it down?</p>
        <div className="flex justify-end gap-1.5 border-t border-border-default px-2.5 py-2">
          <span className="inline-flex h-6 items-center rounded-md border border-border-strong px-2.25 text-2xs font-semibold text-fg-default">
            Discard
          </span>
          <span className="inline-flex h-6 items-center rounded-md border border-action-run-border bg-action-run-soft px-2.5 text-2xs font-semibold text-action-run-ink">
            Send
          </span>
        </div>
      </div>
    </div>
  );
}

export function WelcomeDemo({ name }: { name: string }) {
  const reduced = useReducedMotion();
  const [clock, setClock] = useState<Clock>(() => (reduced ? { beat: 2, tick: 0 } : { beat: 0, tick: 0 }));
  useEffect(() => {
    if (reduced) return;
    const timer = window.setInterval(() => setClock(nextTick), TICK_MS);
    return () => window.clearInterval(timer);
  }, [reduced]);
  const { beat, tick } = clock;
  // Still, the job shows written.
  const typed = reduced ? DEMO_JOB : DEMO_JOB.slice(0, tick);
  return (
    <div data-stagger className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4.5">
      <div className="flex flex-col gap-1.5">
        {BEATS.map((b, i) => {
          const active = i === beat;
          return (
            <Button
              key={b.title}
              variant="quiet"
              aria-current={active ? 'step' : undefined}
              onClick={() => setClock({ beat: i, tick: 0 })}
              className={cn(
                'relative grid h-auto grid-cols-[26px_minmax(0,1fr)] items-start justify-normal gap-3 overflow-hidden rounded-card border px-3.5 py-3.25 text-left whitespace-normal hover:bg-bg-elevated',
                active ? 'border-border-default bg-bg-elevated shadow-card' : 'border-transparent',
              )}
            >
              <span
                className={cn(
                  'grid size-6.5 place-items-center rounded-full border font-mono text-2xs font-semibold transition-colors duration-(--dur-slow)',
                  active ? 'border-fg-default bg-fg-default text-bg-app' : i < beat ? 'border-fg-default text-fg-default' : 'border-border-default text-fg-muted',
                )}
              >
                {i + 1}
              </span>
              <span className={cn('pt-0.75 text-base font-semibold transition-colors duration-(--dur-slow)', active ? 'text-fg-default' : 'text-fg-muted')}>
                {b.title}
              </span>
              {active && !reduced && (
                // How far the beat has played: the clock's own value.
                <span
                  data-beat-progress
                  aria-hidden
                  className="absolute inset-x-0 bottom-0 h-0.5 origin-left bg-fg-default opacity-70 transition-transform ease-linear"
                  style={{ transform: `scaleX(${tick / BEAT_TICKS[i]})`, transitionDuration: `${TICK_MS}ms` }}
                />
              )}
            </Button>
          );
        })}
      </div>
      <div
        aria-hidden
        className="home-welcome-stage relative grid min-h-68 place-items-center overflow-hidden rounded-panel border border-border-default p-5.5"
      >
        {beat === 0 ? <MiniComposer typed={typed} /> : beat === 1 ? <MiniSetup /> : <MiniTalk name={name} />}
      </div>
    </div>
  );
}

export default WelcomeDemo;
